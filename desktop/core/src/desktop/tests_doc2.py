#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Licensed to Cloudera, Inc. under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  Cloudera, Inc. licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json

from nose.tools import assert_equal, assert_false, assert_true
from django.contrib.auth.models import User

from desktop.lib.django_test_util import make_logged_in_client
from desktop.lib.test_utils import grant_access
from desktop.models import import_saved_beeswax_query, Directory, Document2

from beeswax.models import SavedQuery
from beeswax.design import hql_query


class TestDocument2(object):

  def setUp(self):
    self.client = make_logged_in_client(username="doc2", groupname="doc2", recreate=True, is_superuser=False)
    self.user = User.objects.get(username="doc2")
    grant_access("doc2", "doc2", "beeswax")

    # This creates the user directories for the new user
    response = self.client.get('/desktop/api2/docs/')
    data = json.loads(response.content)
    assert_equal('/', data['document']['path'], data)

    self.home_dir = Document2.objects.get_home_directory(user=self.user)


  def test_trash_directory(self):
    assert_true(Directory.objects.filter(owner=self.user, name=Document2.TRASH_DIR, type='directory').exists())


  def test_document_create(self):
    sql = 'SELECT * FROM sample_07'

    design = hql_query(sql)

    # is_auto
    # is_trashed
    # is_redacted
    old_query = SavedQuery.objects.create(
        type=SavedQuery.TYPES_MAPPING['hql'],
        owner=self.user,
        data=design.dumps(),
        name='See examples',
        desc='Example of old format'
    )

    try:
      new_query = import_saved_beeswax_query(old_query)
      new_query_data = new_query.get_data()

      assert_equal('query-hive', new_query_data['type'])
      assert_equal('See examples', new_query_data['name'])
      assert_equal('Example of old format', new_query_data['description'])

      assert_equal('ready', new_query_data['snippets'][0]['status'])
      assert_equal('See examples', new_query_data['snippets'][0]['name'])
      assert_equal('SELECT * FROM sample_07', new_query_data['snippets'][0]['statement_raw'])

      assert_equal([], new_query_data['snippets'][0]['properties']['settings'])
      assert_equal([], new_query_data['snippets'][0]['properties']['files'])
      assert_equal([], new_query_data['snippets'][0]['properties']['functions'])
    finally:
      old_query.delete()


  def test_directory_create(self):
    response = self.client.post('/desktop/api2/doc/mkdir', {'parent_uuid': json.dumps(self.home_dir.uuid), 'name': json.dumps('test_mkdir')})
    data = json.loads(response.content)

    assert_equal(0, data['status'], data)
    assert_true('directory' in data)
    assert_equal(data['directory']['name'], 'test_mkdir', data)
    assert_equal(data['directory']['type'], 'directory', data)


  def test_directory_move(self):
    response = self.client.post('/desktop/api2/doc/mkdir', {'parent_uuid': json.dumps(self.home_dir.uuid), 'name': json.dumps('test_mv')})
    data = json.loads(response.content)
    assert_equal(0, data['status'], data)

    response = self.client.post('/desktop/api2/doc/mkdir', {'parent_uuid': json.dumps(self.home_dir.uuid), 'name': json.dumps('test_mv_dst')})
    data = json.loads(response.content)
    assert_equal(0, data['status'], data)

    response = self.client.post('/desktop/api2/doc/move', {
        'source_doc_uuid': json.dumps(Directory.objects.get(owner=self.user, name='test_mv').uuid),
        'destination_doc_uuid': json.dumps(Directory.objects.get(owner=self.user, name='test_mv_dst').uuid)
    })
    data = json.loads(response.content)

    assert_equal(0, data['status'], data)
    assert_equal(Directory.objects.get(name='test_mv', owner=self.user).path, '/test_mv_dst/test_mv')


  def test_directory_children(self):
    # Creates 2 directories and 2 queries and saves to home directory
    dir1 = Directory.objects.create(name='test_dir1', owner=self.user)
    dir2 = Directory.objects.create(name='test_dir2', owner=self.user)
    query1 = Document2.objects.create(name='query1.sql', type='query-hive', owner=self.user, data={})
    query2 = Document2.objects.create(name='query2.sql', type='query-hive', owner=self.user, data={})
    children = [dir1, dir2, query1, query2]

    self.home_dir.children.add(*children)

    # Test that all children directories and documents are returned
    response = self.client.get('/desktop/api2/docs', {'path': '/'})
    data = json.loads(response.content)
    assert_true('children' in data)
    assert_equal(5, data['count'])  # This includes the 4 docs and .Trash

    # Test filter type
    response = self.client.get('/desktop/api2/docs', {'path': '/', 'type': ['directory']})
    data = json.loads(response.content)
    assert_equal(['directory'], data['types'])
    assert_equal(3, data['count'])
    assert_true(all(doc['type'] == 'directory' for doc in data['children']))

    # Test search text
    response = self.client.get('/desktop/api2/docs', {'path': '/', 'text': 'query'})
    data = json.loads(response.content)
    assert_equal('query', data['text'])
    assert_equal(2, data['count'])
    assert_true(all('query' in doc['name'] for doc in data['children']))

    # Test pagination with limit
    response = self.client.get('/desktop/api2/docs', {'path': '/', 'page': 2, 'limit': 2})
    data = json.loads(response.content)
    assert_equal(5, data['count'])
    assert_equal(2, len(data['children']))


  def test_document_trash(self):
    # Create document under home and directory under home with child document
    dir = Directory.objects.create(name='test_dir', owner=self.user, parent_directory=self.home_dir)
    nested_query = Document2.objects.create(name='query1.sql', type='query-hive', owner=self.user, data={}, parent_directory=dir)
    query = Document2.objects.create(name='query2.sql', type='query-hive', owner=self.user, data={}, parent_directory=self.home_dir)

    # Test that .Trash is currently empty
    response = self.client.get('/desktop/api2/docs', {'path': '/.Trash'})
    data = json.loads(response.content)
    assert_equal(0, data['count'])

    # Delete document
    response = self.client.post('/desktop/api2/doc/delete', {'uuid': json.dumps(query.uuid)})
    data = json.loads(response.content)
    assert_equal(0, data['status'])

    response = self.client.get('/desktop/api2/docs', {'path': '/.Trash'})
    data = json.loads(response.content)
    assert_equal(1, data['count'])
    assert_equal(data['children'][0]['uuid'], query.uuid)

    # Delete directory
    response = self.client.post('/desktop/api2/doc/delete', {'uuid': json.dumps(dir.uuid)})
    data = json.loads(response.content)
    assert_equal(0, data['status'], data)

    response = self.client.get('/desktop/api2/docs', {'path': '/.Trash'})
    data = json.loads(response.content)
    assert_equal(2, data['count'])

    # Verify that only doc in home is .Trash
    response = self.client.get('/desktop/api2/docs', {'path': '/'})
    data = json.loads(response.content)
    assert_true('children' in data)
    assert_equal(1, data['count'])
    assert_equal(Document2.TRASH_DIR, data['children'][0]['name'])


  def test_validations(self):
    # Test invalid names
    invalid_name = '/invalid'
    response = self.client.post('/desktop/api2/doc/mkdir', {'parent_uuid': json.dumps(self.home_dir.uuid), 'name': json.dumps(invalid_name)})
    data = json.loads(response.content)
    assert_equal(-1, data['status'], data)
    assert_true('invalid character' in data['message'])

    # Test error on creating documents with same name and location
    test_dir = Directory.objects.create(name='test_dir', owner=self.user, parent_directory=self.home_dir)
    response = self.client.post('/desktop/api2/doc/mkdir', {'parent_uuid': json.dumps(self.home_dir.uuid), 'name': json.dumps('test_dir')})
    data = json.loads(response.content)
    assert_equal(-1, data['status'], data)
    assert_true('/test_dir already exists' in data['message'])

    # But can create same name in different location
    response = self.client.post('/desktop/api2/doc/mkdir', {'parent_uuid': json.dumps(test_dir.uuid), 'name': json.dumps('test_dir')})
    data = json.loads(response.content)
    assert_equal(0, data['status'], data)

    # Test that home and Trash directories cannot be recreated or modified
    response = self.client.post('/desktop/api2/doc/mkdir', {'parent_uuid': json.dumps(test_dir.uuid), 'name': json.dumps(Document2.TRASH_DIR)})
    data = json.loads(response.content)
    assert_equal(-1, data['status'], data)
    assert_equal('Cannot create or modify the home or .Trash directory.', data['message'])

    response = self.client.post('/desktop/api2/doc/move', {
        'source_doc_uuid': json.dumps(self.home_dir.uuid),
        'destination_doc_uuid': json.dumps(test_dir.uuid)
    })
    data = json.loads(response.content)
    assert_equal(-1, data['status'], data)
    assert_equal('Cannot create or modify the home or .Trash directory.', data['message'])

    trash_dir = Directory.objects.get(name=Document2.TRASH_DIR, owner=self.user)
    response = self.client.post('/desktop/api2/doc/move', {
        'source_doc_uuid': json.dumps(trash_dir.uuid),
        'destination_doc_uuid': json.dumps(test_dir.uuid)
    })
    data = json.loads(response.content)
    assert_equal(-1, data['status'], data)
    assert_equal('Cannot create or modify the home or .Trash directory.', data['message'])